'''
Reference:
https://towardsdatascience.com/implement-canny-edge-detection-from-scratch-with-pytorch-a1cccfa58bed
'''
import numpy as np
import torch
import torch.nn as nn
import torchvision.transforms as transforms 
import cv2

def get_gaussian_kernel(k=3, mu=0, sigma=1, normalize=True):
    # compute 1 dimension gaussian
    gaussian_1D = np.linspace(-1, 1, k)
    # compute a grid distance from center
    x, y = np.meshgrid(gaussian_1D, gaussian_1D)
    distance = (x ** 2 + y ** 2) ** 0.5

    # compute the 2 dimension gaussian
    gaussian_2D = np.exp(-(distance - mu) ** 2 / (2 * sigma ** 2))
    gaussian_2D = gaussian_2D / (2 * np.pi *sigma **2)

    # normalize part (mathematically)
    if normalize:
        gaussian_2D = gaussian_2D / np.sum(gaussian_2D)
    return gaussian_2D

def get_sobel_kernel(k=3):
    # get range
    range = np.linspace(-(k // 2), k // 2, k)
    # compute a grid the numerator and the axis-distances
    x, y = np.meshgrid(range, range)
    sobel_2D_numerator = x
    sobel_2D_denominator = (x ** 2 + y ** 2)
    sobel_2D_denominator[:, k // 2] = 1  # avoid division by zero
    sobel_2D = sobel_2D_numerator / sobel_2D_denominator
    return sobel_2D


def get_thin_kernels(start=0, end=360, step=45):
        k_thin = 3  # actual size of the directional kernel
        # increase for a while to avoid interpolation when rotating
        k_increased = k_thin + 2

        # get 0° angle directional kernel
        thin_kernel_0 = np.zeros((k_increased, k_increased))
        thin_kernel_0[k_increased // 2, k_increased // 2] = 1
        thin_kernel_0[k_increased // 2, k_increased // 2 + 1:] = -1

        # rotate the 0° angle directional kernel to get the other ones
        thin_kernels = []
        for angle in range(start, end, step):
            (h, w) = thin_kernel_0.shape
            # get the center to not rotate around the (0, 0) coord point
            center = (w // 2, h // 2)
            # apply rotation
            rotation_matrix = cv2.getRotationMatrix2D(center, angle, 1)
            kernel_angle_increased = cv2.warpAffine(thin_kernel_0, rotation_matrix, (w, h), cv2.INTER_NEAREST)

            # get the k=3 kerne
            kernel_angle = kernel_angle_increased[1:-1, 1:-1]
            is_diag = (abs(kernel_angle) == 1)      # because of the interpolation
            kernel_angle = kernel_angle * is_diag   # because of the interpolation
            thin_kernels.append(kernel_angle)
        return thin_kernels


def write_to_pt_file(data, filename, print_data=False):
    torch.save(data, filename)
    if print_data:
        print(data) 


class CannyFilter(nn.Module):
    def __init__(self,
                 k_gaussian=3,
                 mu=0,
                 sigma=1,
                 k_sobel=3,
                 use_cuda=False):
        super(CannyFilter, self).__init__()
        # device
        self.device = 'cuda' if use_cuda else 'cpu'

        # sobel
        sobel_2D = get_sobel_kernel(k_sobel)
        self.sobel_filter_x = nn.Conv2d(in_channels=1,
                                        out_channels=1,
                                        kernel_size=k_sobel,
                                        padding=k_sobel // 2,
                                        bias=False)
        self.sobel_filter_y = nn.Conv2d(in_channels=1,
                                        out_channels=1,
                                        kernel_size=k_sobel,
                                        padding=k_sobel // 2,
                                        bias=False)
        with torch.no_grad(): 
            self.sobel_filter_x.weight.copy_(
            torch.from_numpy(sobel_2D).unsqueeze(0).unsqueeze(0).float())
        with torch.no_grad(): 
            self.sobel_filter_y.weight.copy_(
            torch.from_numpy(sobel_2D.T).unsqueeze(0).unsqueeze(0).float())


        # thin
        thin_kernels = get_thin_kernels()
        directional_kernels = np.stack(thin_kernels)
        self.directional_filter = nn.Conv2d(in_channels=1,
                                            out_channels=8,
                                            kernel_size=thin_kernels[0].shape,
                                            padding=thin_kernels[0].shape[-1] // 2,
                                            bias=False)
        with torch.no_grad(): 
            self.directional_filter.weight.copy_(
            torch.from_numpy(directional_kernels).unsqueeze(1).float())

        # hysteresis
        hysteresis = np.ones((3, 3)) + 0.25
        self.hysteresis = nn.Conv2d(in_channels=1,
                                    out_channels=1,
                                    kernel_size=3,
                                    padding=1,
                                    bias=False)
        with torch.no_grad(): 
            self.hysteresis.weight.copy_(
            torch.from_numpy(hysteresis).unsqueeze(0).unsqueeze(0).float())


    def forward(self, img, low_threshold=None, high_threshold=None, hysteresis=False, 
                use_sa=False, grad_x_sa=0, grad_y_sa=0):
        # set the setps tensors
        B, C, H, W = img.shape
        grad_x = torch.zeros((B, 1, H, W)).to(self.device)
        grad_y = torch.zeros((B, 1, H, W)).to(self.device)
        grad_magnitude = torch.zeros((B, 1, H, W)).to(self.device)
        grad_orientation = torch.zeros((B, 1, H, W)).to(self.device)

        # sobel
        if use_sa: # caculate the grads with Systolic Array 
            grad_x = grad_x_sa
            grad_y = grad_y_sa
        else: # calculate the grads with Python
            for c in range(C):
                soble_result_x = self.sobel_filter_x(img[:, c:c+1])
                soble_result_y = self.sobel_filter_y(img[:, c:c+1])
                grad_x = grad_x + soble_result_x
                grad_y = grad_y + soble_result_y
                write_to_pt_file(img[:, c:c+1], f'img_{c}.pt')
                write_to_pt_file(soble_result_x, f'soble_result_x_{c}.pt')
                write_to_pt_file(soble_result_y, f'soble_result_y_{c}.pt')
            write_to_pt_file(self.sobel_filter_x.weight, f'soble_filter_x_weight.pt')
            write_to_pt_file(self.sobel_filter_y.weight, f'soble_filter_y_weight.pt')

        # thick edges
        grad_x, grad_y = grad_x / C, grad_y / C
        grad_magnitude = (grad_x ** 2 + grad_y ** 2) ** 0.5
        grad_orientation = torch.atan(grad_y / grad_x)
        grad_orientation = grad_orientation * (360 / np.pi) + 180 # convert to degree
        grad_orientation = torch.round(grad_orientation / 45) * 45  # keep a split by 45

        # thin edges
        directional = self.directional_filter(grad_magnitude)
        # get indices of positive and negative directions
        positive_idx = (grad_orientation / 45) % 8
        negative_idx = ((grad_orientation / 45) + 4) % 8
        thin_edges = grad_magnitude.clone()
        # non maximum suppression direction by direction
        for pos_i in range(4):
            neg_i = pos_i + 4
            # get the oriented grad for the angle
            is_oriented_i = (positive_idx == pos_i) * 1
            is_oriented_i = is_oriented_i + (positive_idx == neg_i) * 1
            pos_directional = directional[:, pos_i]
            neg_directional = directional[:, neg_i]
            selected_direction = torch.stack([pos_directional, neg_directional])
            # get the local maximum pixels for the angle
            is_max = selected_direction.min(dim=0)[0] > 0.0
            is_max = torch.unsqueeze(is_max, dim=1)
            # apply non maximum suppression
            to_remove = (is_max == 0) * 1 * (is_oriented_i) > 0
            thin_edges[to_remove] = 0.0

        # thresholds
        if low_threshold is not None:
            low = thin_edges > low_threshold
            if high_threshold is not None:
                high = thin_edges > high_threshold
                # get black/gray/white only
                thin_edges = low * 0.5 + high * 0.5
                if hysteresis:
                    # get weaks and check if they are high or not
                    weak = (thin_edges == 0.5) * 1
                    weak_is_high = (self.hysteresis(thin_edges) > 1) * weak
                    thin_edges = high * 1 + weak_is_high * 1
            else:
                thin_edges = low * 1
        return grad_x, grad_y, grad_magnitude, grad_orientation, thin_edges
    

def main():
    # Load the input image 
    image = cv2.imread('rubiks_cube.jpg') 
    image = cv2.resize(image, (256, 256))  # original 256*256
    
    gray_image = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)


    # Convert the image to Torch tensor 
    img_tensor = torch.from_numpy(image)  #  transform(image)
    img_tensor = img_tensor.permute(2, 0, 1).unsqueeze(0)

    # Run inference
    model = CannyFilter()
    grad_x, grad_y, grad_magnitude, grad_orientation, thin_edges = model(img_tensor.float())

    # Save image results
    cv2.imwrite('edge_rubiks_cube.jpg', grad_magnitude[0].permute(1, 2, 0).detach().numpy())


if __name__ == '__main__':
    main()